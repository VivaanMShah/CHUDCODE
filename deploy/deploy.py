"""Build the Docker image in AWS, then roll it out to ECS. Run this after any change:

    python -m deploy.deploy

Steps: make sure the AWS resources exist -> zip the source -> AWS CodeBuild builds the image and pushes
it to ECR -> ECS starts the new version and stops the old one -> print the link.
Credentials come from .env (or your environment). They are never printed or put in the image.
"""
import io
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime

import botocore.exceptions as bx

from .aws_setup import PORT, ensure_all
from .common import NAME, REGION, ROOT, client, say

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
        for folder in ("app", "src"):
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


if __name__ == "__main__":
    t0 = time.time()
    res = ensure_all()
    tag = f"{datetime.now():%Y%m%d-%H%M%S}-{git_short()}"
    build_image(res, tag)
    roll_out(res, task_definition(res, tag))
    url = f"http://{res['alb_dns']}"
    say("Checking the live site")
    ok = wait_healthy(url)
    say(f"\n{'Live' if ok else 'Deployed, but /health is not answering yet'}: {url}   ({(time.time() - t0) / 60:.1f} min)")
