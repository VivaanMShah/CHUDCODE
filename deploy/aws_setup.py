"""Create (or reuse) every AWS resource the app needs. Safe to run again: it only creates what is missing.

    python -m deploy.aws_setup

Creates: a second subnet, security groups, an ECR repository, an S3 bucket for build sources,
log groups, three IAM roles, a CodeBuild project (builds the Docker image, because this PC has no
Docker), an Application Load Balancer, and an ECS cluster. The ECS service itself is created by
deploy.py after the first image exists.
"""
import json
import time

import botocore.exceptions as bx

from .common import NAME, REGION, account_id, client, load_env, say

PORT = 8000


def _ignore(code: str, fn):
    """Run fn, ignoring one named AWS error (for 'already exists')."""
    try:
        return fn()
    except bx.ClientError as e:
        if e.response["Error"]["Code"] != code:
            raise


# ---------------------------------------------------------------- network

def ensure_network() -> dict:
    ec2 = client("ec2")
    vpc = ec2.describe_vpcs()["Vpcs"][0]["VpcId"]  # the sandbox account has exactly one
    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc]}])["Subnets"]
    # Public route table = the one with a route to an internet gateway
    rt = next(r["RouteTableId"] for r in ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc]}])["RouteTables"]
              if any(x.get("GatewayId", "").startswith("igw-") for x in r["Routes"]))
    have_azs = {s["AvailabilityZone"] for s in subnets}
    ids = [s["SubnetId"] for s in subnets if s["MapPublicIpOnLaunch"]]
    if len(have_azs) < 2:  # a load balancer needs two availability zones
        az = next(z["ZoneName"] for z in ec2.describe_availability_zones()["AvailabilityZones"] if z["ZoneName"] not in have_azs)
        new = ec2.create_subnet(VpcId=vpc, CidrBlock="10.0.2.0/24", AvailabilityZone=az,
                                TagSpecifications=[{"ResourceType": "subnet", "Tags": [{"Key": "Name", "Value": f"{NAME}-b"}]}])["Subnet"]["SubnetId"]
        ec2.modify_subnet_attribute(SubnetId=new, MapPublicIpOnLaunch={"Value": True})
        ec2.associate_route_table(RouteTableId=rt, SubnetId=new)
        ids.append(new)
        say(f"  created second subnet in {az}")
    return {"vpc": vpc, "subnets": ids}


def ensure_sg(vpc: str, name: str, desc: str, rules: list) -> str:
    ec2 = client("ec2")
    found = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": [name]}, {"Name": "vpc-id", "Values": [vpc]}])["SecurityGroups"]
    sg = found[0]["GroupId"] if found else ec2.create_security_group(GroupName=name, Description=desc, VpcId=vpc)["GroupId"]
    for perm in rules:
        _ignore("InvalidPermission.Duplicate", lambda perm=perm: ec2.authorize_security_group_ingress(GroupId=sg, IpPermissions=[perm]))
    return sg


# ---------------------------------------------------------------- IAM

def ensure_role(name: str, service: str, managed: list[str], inline: dict | None) -> str:
    iam = client("iam")
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": service}, "Action": "sts:AssumeRole"}]}
    try:
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
    except bx.ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
        arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
        say(f"  created role {name}")
    for m in managed:
        iam.attach_role_policy(RoleName=name, PolicyArn=m)
    if inline:
        iam.put_role_policy(RoleName=name, PolicyName=f"{name}-inline", PolicyDocument=json.dumps(inline))
    return arn


# ---------------------------------------------------------------- main

def ensure_all() -> dict:
    load_env()
    acct = account_id()
    res: dict = {"account": acct, "region": REGION}
    say(f"Account {acct} in {REGION}")

    say("Network")
    res.update(ensure_network())
    res["alb_sg"] = ensure_sg(res["vpc"], f"{NAME}-alb", "Load balancer", [
        {"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
    res["task_sg"] = ensure_sg(res["vpc"], f"{NAME}-task", "App containers", [
        {"IpProtocol": "tcp", "FromPort": PORT, "ToPort": PORT, "UserIdGroupPairs": [{"GroupId": res["alb_sg"]}]}])

    say("Image registry and build bucket")
    ecr = client("ecr")
    _ignore("RepositoryAlreadyExistsException", lambda: ecr.create_repository(repositoryName=NAME))
    ecr.put_lifecycle_policy(repositoryName=NAME, lifecyclePolicyText=json.dumps({"rules": [{
        "rulePriority": 1, "description": "keep the last 10 images", "selection": {
            "tagStatus": "any", "countType": "imageCountMoreThan", "countNumber": 10}, "action": {"type": "expire"}}]}))
    res["repo_uri"] = ecr.describe_repositories(repositoryNames=[NAME])["repositories"][0]["repositoryUri"]
    res["bucket"] = f"{NAME}-build-{acct}-{REGION}"
    s3 = client("s3")
    _ignore("BucketAlreadyOwnedByYou", lambda: s3.create_bucket(Bucket=res["bucket"]))
    s3.put_public_access_block(Bucket=res["bucket"], PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})

    say("Logs")
    logs = client("logs")
    for g in (f"/ecs/{NAME}", f"/codebuild/{NAME}"):
        _ignore("ResourceAlreadyExistsException", lambda g=g: logs.create_log_group(logGroupName=g))
        logs.put_retention_policy(logGroupName=g, retentionInDays=14)

    say("IAM roles")
    res["exec_role"] = ensure_role(f"{NAME}-exec", "ecs-tasks.amazonaws.com",
                                   ["arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"], None)
    res["task_role"] = ensure_role(f"{NAME}-task", "ecs-tasks.amazonaws.com", [], {
        "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Resource": "*", "Action": [
            "bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:Converse", "bedrock:ConverseStream"]}]})
    res["build_role"] = ensure_role(f"{NAME}-build", "codebuild.amazonaws.com", [], {
        "Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
            {"Effect": "Allow", "Resource": f"arn:aws:ecr:{REGION}:{acct}:repository/{NAME}", "Action": [
                "ecr:BatchCheckLayerAvailability", "ecr:CompleteLayerUpload", "ecr:InitiateLayerUpload",
                "ecr:PutImage", "ecr:UploadLayerPart", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]},
            {"Effect": "Allow", "Action": ["s3:GetObject", "s3:GetObjectVersion"], "Resource": f"arn:aws:s3:::{res['bucket']}/*"},
            {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"], "Resource": "*"}]})
    time.sleep(10)  # let new roles propagate

    say("Build project")
    cb = client("codebuild")
    project = dict(
        name=NAME,
        source={"type": "S3", "location": f"{res['bucket']}/source/source.zip"},
        artifacts={"type": "NO_ARTIFACTS"},
        environment={"type": "LINUX_CONTAINER", "image": "aws/codebuild/standard:7.0",
                     "computeType": "BUILD_GENERAL1_MEDIUM", "privilegedMode": True},
        serviceRole=res["build_role"], timeoutInMinutes=30,
        logsConfig={"cloudWatchLogs": {"status": "ENABLED", "groupName": f"/codebuild/{NAME}"}})
    try:
        cb.create_project(**project)
        say("  created build project")
    except bx.ClientError as e:
        if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
            raise
        cb.update_project(**project)

    say("Load balancer")
    elb = client("elbv2")
    try:
        lb = elb.describe_load_balancers(Names=[f"{NAME}-alb"])["LoadBalancers"][0]
    except bx.ClientError as e:
        if e.response["Error"]["Code"] != "LoadBalancerNotFound":
            raise
        lb = elb.create_load_balancer(Name=f"{NAME}-alb", Subnets=res["subnets"], SecurityGroups=[res["alb_sg"]],
                                      Scheme="internet-facing", Type="application")["LoadBalancers"][0]
        say("  created load balancer")
    res["alb_arn"], res["alb_dns"] = lb["LoadBalancerArn"], lb["DNSName"]
    try:
        tg = elb.describe_target_groups(Names=[f"{NAME}-tg"])["TargetGroups"][0]
    except bx.ClientError as e:
        if e.response["Error"]["Code"] != "TargetGroupNotFound":
            raise
        tg = elb.create_target_group(Name=f"{NAME}-tg", Protocol="HTTP", Port=PORT, VpcId=res["vpc"], TargetType="ip",
                                     HealthCheckPath="/health", HealthCheckIntervalSeconds=15, HealthyThresholdCount=2,
                                     UnhealthyThresholdCount=3, Matcher={"HttpCode": "200"})["TargetGroups"][0]
        elb.modify_target_group_attributes(TargetGroupArn=tg["TargetGroupArn"], Attributes=[
            {"Key": "deregistration_delay.timeout_seconds", "Value": "15"}])
    res["tg_arn"] = tg["TargetGroupArn"]
    if not elb.describe_listeners(LoadBalancerArn=res["alb_arn"])["Listeners"]:
        elb.create_listener(LoadBalancerArn=res["alb_arn"], Protocol="HTTP", Port=80,
                            DefaultActions=[{"Type": "forward", "TargetGroupArn": res["tg_arn"]}])

    say("ECS cluster")
    client("ecs").create_cluster(clusterName=NAME)  # idempotent
    say("Ready.")
    return res


if __name__ == "__main__":
    out = ensure_all()
    say(f"Link (after the first deploy): http://{out['alb_dns']}")
