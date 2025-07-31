import os
import boto3
from agent_util import KnowledgeBaseSaver

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext

s3 = boto3.client("s3")
# Initialize powertools
logger = Logger()
tracer = Tracer()


saver = KnowledgeBaseSaver(
    knowledge_base_id=os.environ["STRANDS_KNOWLEDGE_BASE_ID"],
    bypass_tool_consent=os.environ.get("BYPASS_TOOL_CONSENT", "True"),
)


@logger.inject_lambda_context(log_event=True)
@tracer.capture_lambda_handler
def lambda_handler(event, context: LambdaContext):
    logger.info("Received extracted text event")
    logger.info(f"received event {event}")
    logger.info(f"text {event['text']}")
    # saves to kb with strands agent
    result = saver.store_text(
        event["text"],
        metadata={"source": "textract-lambda", "userId": "UserID"},
    )
    logger.info("Stored transcript in KB: %s", result)