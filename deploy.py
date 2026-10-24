"""Host the app on AWS (ECS + ECR + load balancer + CloudFront). Credentials come from .env or the environment.

  python deploy.py          build the image in AWS, roll it out, print the link (run after any change)
  python deploy.py setup    only create the AWS resources (safe to repeat)
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import zipfile

import boto3
import botocore.exceptions as bx


# ============================================================================
# SHARED HELPERS
# Shared helpers for the AWS scripts. Credentials come from .env or the environment and are never printed.
# ============================================================================

ROOT = Path(__file__).resolve().parent
NAME = "outage-concierge"
REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"


def load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2).strip("\"'")


def client(service: str):
    return boto3.client(service, region_name=REGION)


def account_id() -> str:
    return client("sts").get_caller_identity()["Account"]


def say(msg: str) -> None:
    print(msg, flush=True)

# ============================================================================
# AWS RESOURCES (created if missing)
# Create (or reuse) every AWS resource the app needs. Safe to run again: it only creates what is missing.
# ============================================================================

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


# ---------------------------------------------------------------- HTTPS

def ensure_https(alb_dns: str) -> dict:
    """CloudFront in front of the load balancer gives a free https:// address (the ALB itself can't, without a domain)."""
    cf = client("cloudfront")
    for page in cf.get_paginator("list_distributions").paginate():
        for d in (page.get("DistributionList") or {}).get("Items") or []:
            if d["Comment"] == NAME:
                return {"cf_id": d["Id"], "https_url": f"https://{d['DomainName']}"}
    cfg = {
        "CallerReference": f"{NAME}-{int(time.time())}", "Comment": NAME, "Enabled": True, "PriceClass": "PriceClass_100",
        "Origins": {"Quantity": 1, "Items": [{"Id": "alb", "DomainName": alb_dns, "CustomOriginConfig": {
            "HTTPPort": 80, "HTTPSPort": 443, "OriginProtocolPolicy": "http-only", "OriginReadTimeout": 30}}]},
        "DefaultCacheBehavior": {
            "TargetOriginId": "alb", "ViewerProtocolPolicy": "redirect-to-https", "Compress": True,
            "AllowedMethods": {"Quantity": 7, "Items": ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"],
                               "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}},
            "CachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",          # managed: CachingDisabled
            "OriginRequestPolicyId": "b689b0a8-53d0-40ab-baf2-68738e2966ac"},  # managed: AllViewerExceptHostHeader
        "ViewerCertificate": {"CloudFrontDefaultCertificate": True},
    }
    d = cf.create_distribution(DistributionConfig=cfg)["Distribution"]
    say("  created CloudFront distribution")
    return {"cf_id": d["Id"], "https_url": f"https://{d['DomainName']}"}


def wait_deployed(cf_id: str) -> None:
    cf = client("cloudfront")
    for _ in range(60):
        if cf.get_distribution(Id=cf_id)["Distribution"]["Status"] == "Deployed":
            return
        time.sleep(15)


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

    say("HTTPS address")
    res.update(ensure_https(res["alb_dns"]))
    say("Ready.")
    return res

# ============================================================================
# BUILD AND ROLL OUT
# Build the Docker image in AWS, then roll it out to ECS. Run this after any change:
# ============================================================================

BUILDSPEC = """version: 0.2
phases:
  pre_build:
    commands:
      - aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR_REGISTRY
  build:
    commands:
      - docker build -t $REPO_URI:$IMAGE_TAG -t $REPO_URI:latest .
  post_build:
    commands:
      - docker push $REPO_URI:$IMAGE_TAG
      - docker push $REPO_URI:latest
"""

TOP_FILES = ["Dockerfile", ".dockerignore", "requirements-prod.txt", "package.json", "package-lock.json",
             "tsconfig.json", "vite.config.ts", "index.html"]
SKIP = {"__pycache__"}


def make_zip() -> bytes:
    store = ROOT / "data" / "_cache" / "store.pkl"
    if not store.exists():
        sys.exit("No processed data found. Run `python -m app.data` once first (it needs the data/ folder).")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in TOP_FILES:
            z.write(ROOT / f, f)
        for folder in ("app", "src", "public"):
            for p in (ROOT / folder).rglob("*"):
                rel = p.relative_to(ROOT).as_posix()
                if not p.is_file() or SKIP & set(p.parts) or p.suffix == ".pyc" or rel == "src/main.py":
                    continue
                z.write(p, rel)
        z.write(store, "bundle/store.pkl")  # processed data only (about 150 KB), not the raw files
        z.writestr("buildspec.yml", BUILDSPEC)
    return buf.getvalue()


def git_short() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:  # noqa: BLE001
        return "local"


def build_image(res: dict, tag: str) -> None:
    data = make_zip()
    say(f"Uploading source ({len(data) / 1024:.0f} KB)")
    client("s3").put_object(Bucket=res["bucket"], Key="source/source.zip", Body=data)
    cb = client("codebuild")
    env = [{"name": "REPO_URI", "value": res["repo_uri"]}, {"name": "IMAGE_TAG", "value": tag},
           {"name": "ECR_REGISTRY", "value": res["repo_uri"].split("/")[0]}]
    build_id = cb.start_build(projectName=NAME, environmentVariablesOverride=env)["build"]["id"]
    say(f"Building image {tag} in AWS (about 3 to 6 minutes)")
    last = ""
    while True:
        b = cb.batch_get_builds(ids=[build_id])["builds"][0]
        phase = b.get("currentPhase", "")
        if phase != last:
            say(f"  {phase}")
            last = phase
        if b["buildStatus"] != "IN_PROGRESS":
            break
        time.sleep(10)
    if b["buildStatus"] != "SUCCEEDED":
        say(f"Build {b['buildStatus']}. Last log lines:")
        try:
            ev = client("logs").get_log_events(logGroupName=f"/codebuild/{NAME}", logStreamName=b["logs"]["streamName"],
                                               limit=60, startFromHead=False)["events"]
            say("\n".join(e["message"].rstrip() for e in ev[-40:]))
        except bx.ClientError:
            pass
        sys.exit(1)


def task_definition(res: dict, tag: str) -> str:
    ecs = client("ecs")
    return ecs.register_task_definition(
        family=NAME, networkMode="awsvpc", requiresCompatibilities=["FARGATE"], cpu="1024", memory="2048",
        executionRoleArn=res["exec_role"], taskRoleArn=res["task_role"],
        containerDefinitions=[{
            "name": "app", "image": f"{res['repo_uri']}:{tag}", "essential": True,
            "portMappings": [{"containerPort": PORT, "protocol": "tcp"}],
            "environment": [
                {"name": "AWS_REGION", "value": REGION},
                {"name": "LLM_PROVIDER", "value": "bedrock"},
                {"name": "CORS_ORIGINS", "value": ""},  # the site and API share one address
                {"name": "AUDIT_TO_STDOUT", "value": "1"},
            ],
            "logConfiguration": {"logDriver": "awslogs", "options": {
                "awslogs-group": f"/ecs/{NAME}", "awslogs-region": REGION, "awslogs-stream-prefix": "app"}},
        }])["taskDefinition"]["taskDefinitionArn"]


def roll_out(res: dict, taskdef: str) -> None:
    ecs = client("ecs")
    existing = ecs.describe_services(cluster=NAME, services=[NAME])["services"]
    if existing and existing[0]["status"] == "ACTIVE":
        ecs.update_service(cluster=NAME, service=NAME, taskDefinition=taskdef, forceNewDeployment=True)
        say("Updating the running service")
    else:
        ecs.create_service(
            cluster=NAME, serviceName=NAME, taskDefinition=taskdef, desiredCount=1, launchType="FARGATE",
            networkConfiguration={"awsvpcConfiguration": {"subnets": res["subnets"], "securityGroups": [res["task_sg"]],
                                                          "assignPublicIp": "ENABLED"}},
            loadBalancers=[{"targetGroupArn": res["tg_arn"], "containerName": "app", "containerPort": PORT}],
            healthCheckGracePeriodSeconds=90,
            deploymentConfiguration={"minimumHealthyPercent": 100, "maximumPercent": 200,
                                     "deploymentCircuitBreaker": {"enable": True, "rollback": True}})
        say("Created the service")
    say("Waiting for the new version to start (about 2 minutes)")
    deadline = time.time() + 15 * 60
    while time.time() < deadline:
        s = ecs.describe_services(cluster=NAME, services=[NAME])["services"][0]
        d = next((x for x in s["deployments"] if x["status"] == "PRIMARY"), None)
        if d and d.get("rolloutState") == "COMPLETED" and len(s["deployments"]) == 1:
            return
        if d and d.get("rolloutState") == "FAILED":
            say(f"Rollout failed: {d.get('rolloutStateReason')}")
            for e in s["events"][:5]:
                say("  " + e["message"])
            sys.exit(1)
        time.sleep(15)
    sys.exit("Timed out waiting for the rollout. Check the ECS console.")


def wait_healthy(url: str) -> bool:
    for _ in range(24):
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=8) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(5)
    return False


def setup_only() -> None:
    out = ensure_all()
    say("Waiting for CloudFront to finish (a few minutes the first time)")
    wait_deployed(out["cf_id"])
    say(f"HTTPS link: {out['https_url']}")
    say(f"Plain link: http://{out['alb_dns']}")


def deploy_main() -> None:
    t0 = time.time()
    res = ensure_all()
    tag = f"{datetime.now():%Y%m%d-%H%M%S}-{git_short()}"
    build_image(res, tag)
    roll_out(res, task_definition(res, tag))
    url = f"http://{res['alb_dns']}"
    say("Checking the live site")
    ok = wait_healthy(url)
    say(f"\n{'Live' if ok else 'Deployed, but /health is not answering yet'}: {res['https_url']}   ({(time.time() - t0) / 60:.1f} min)")
    say(f"Plain http link: {url}")


if __name__ == "__main__":
    if sys.argv[1:] == ["setup"]:
        setup_only()
    else:
        deploy_main()
