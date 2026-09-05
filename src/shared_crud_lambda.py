"""
Shared CRUD handler invoked identically behind an ALB target group and an
API Gateway REST API (Lambda proxy integration on both sides).

Why one function works behind both:
Both ALB (with Lambda targets, proxy-style) and API Gateway (Lambda proxy
integration) invoke Lambda with a broadly similar "proxy" event shape and
expect the same structured response back: statusCode, headers, body. The
two event shapes differ in a few details (API Gateway nests query/path
params slightly differently, and ALB's requestContext has an "elb" key
instead of API Gateway's "apiId"/"resourcePath"), but neither difference
matters for this handler, since it only reads the JSON body regardless of
which front door the request came through. This is the same "operation"
router pattern used in the Lambda + DynamoDB Basics challenge, reused here
unchanged behind two different entry points.
"""

from __future__ import print_function
import json
import os
import boto3

TABLE_NAME = os.environ.get("TABLE_NAME", "alb-apigw-comparison")
ddb = boto3.resource("dynamodb").Table(TABLE_NAME)


def _response(status_code, payload):
    """
    Structured response format required by BOTH ALB (Lambda target,
    multiValueHeaders optional) and API Gateway (Lambda proxy integration).
    Using this single shape keeps the handler front-door-agnostic.
    """
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def lambda_handler(event, context):
    # Identify which front door invoked this, purely for logging/comparison
    # purposes during the load test -- the business logic below never
    # branches on this.
    request_context = event.get("requestContext", {})
    source = "ALB" if "elb" in request_context else "API Gateway"
    print(f"Invoked via: {source}")

    try:
        body = json.loads(event.get("body") or "{}")
    except (TypeError, json.JSONDecodeError):
        return _response(400, {"error": "Malformed JSON body"})

    operation = body.get("operation", "list")

    try:
        if operation == "list":
            result = ddb.scan()
            return _response(200, {"source": source, "items": result.get("Items", [])})

        if operation == "create":
            item = body.get("item")
            if not item or "id" not in item:
                return _response(400, {"error": "item with 'id' is required for create"})
            ddb.put_item(Item=item)
            return _response(200, {"source": source, "message": "Item created", "id": item["id"]})

        if operation == "read":
            item_id = body.get("id")
            if not item_id:
                return _response(400, {"error": "'id' is required for read"})
            result = ddb.get_item(Key={"id": item_id})
            item = result.get("Item")
            if not item:
                return _response(404, {"error": "Item not found", "id": item_id})
            return _response(200, {"source": source, "item": item})

        return _response(400, {"error": f"Unrecognized operation '{operation}'"})

    except Exception as exc:  # noqa: BLE001 -- surface any DynamoDB error as a 500
        print(f"Error processing request: {exc}")
        return _response(500, {"error": "Internal error processing request"})
