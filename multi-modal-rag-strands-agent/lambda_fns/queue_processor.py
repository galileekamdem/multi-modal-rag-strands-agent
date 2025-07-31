import json
import os
import boto3
from agent_util import KnowledgeBaseSaver
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.data_classes import (
    SQSEvent,
    SQSRecord,
    event_source,
)

# Initialize powertools
logger = Logger()
tracer = Tracer()


# Initialize AWS clients
s3_client = boto3.client("s3")
sfn_client = boto3.client("stepfunctions")

saver = KnowledgeBaseSaver(
    knowledge_base_id=os.environ["STRANDS_KNOWLEDGE_BASE_ID"],
    bypass_tool_consent=os.environ.get("BYPASS_TOOL_CONSENT", "True"),
)


def process_record(record: SQSRecord):
    message = json.loads(record.body)
    message_id = record.message_id
    return message, message_id


@logger.inject_lambda_context(log_event=True)
@tracer.capture_lambda_handler
@event_source(data_class=SQSEvent)
def lambda_handler(event: SQSEvent, context: LambdaContext):
    """
    Lambda function to process SQS messages containing S3 file upload events.
    This function will invoke the appropriate Step Functions workflow based on the file extension.

    Args:
        event: The SQS event containing S3 file upload information
        context: Lambda context

    Returns:
        A response object with the status and message
    """
    logger.info("Processing SQS messages")

    # Get the state machine ARNs from environment variables
    extract_text_state_machine_arn = os.environ.get("EXTRACT_TEXT_STATE_MACHINE_ARN")
    transcribe_media_state_machine_arn = os.environ.get(
        "TRANSCRIBE_MEDIA_STATE_MACHINE_ARN"
    )

    if not extract_text_state_machine_arn:
        logger.error("EXTRACT_TEXT_STATE_MACHINE_ARN environment variable is not set")
        return

    if not transcribe_media_state_machine_arn:
        logger.error(
            "TRANSCRIBE_MEDIA_STATE_MACHINE_ARN environment variable is not set"
        )
        return

        # Process each record in the SQS event
    for record in event.records:
        try:
            # Parse the SQS message body
            message, message_id = process_record(record)
            logger.debug(f"Processing message: {json.dumps(message)}")

            # Extract file information from the message
            bucket_name = message.get("bucket")
            object_key = message.get("key")
            extension = message.get("extension", "").lower()

            if not bucket_name or not object_key:
                logger.warning("Missing bucket or key in message")
                continue

            if extension in [".md", ".csv", ".txt", ".doc", ".docx"]:
                try:
                    obj = s3_client.get_object(Bucket=bucket_name, Key=object_key)
                    body = obj["Body"].read().decode("utf-8", errors="replace")

                    # Log the entire file or trim if huge
                    logger.info(
                        f"🔹 {object_key} content (first 4 KB shown):\n{body[:4096]}"
                    )

                    # save to knowledge

                    result = saver.store_text(
                        body,
                        metadata={
                            "source": "textract-lambda",
                            "s3_key": object_key,
                            "userId": "UserID",
                        },
                    )
                    logger.info("Stored transcript in KB: %s", result)

                except Exception as e:
                    logger.exception(
                        f"Failed to read {object_key} from s3://{bucket_name}: {e}"
                    )
                # Skip Step Functions for .md / .csv
                continue

            if extension in [".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif"]:
                # Invoke the extract text workflow for document files
                logger.info(f"Invoking extract text workflow for {object_key}")

                filename = os.path.basename(object_key)

                # Prepare input for the Step Functions workflow
                workflow_input = {
                    "bucket_name": bucket_name,
                    "object_key": object_key,
                    "filename": filename,
                    "file_extension": extension,
                }

                # Start the Step Functions execution
                response = sfn_client.start_execution(
                    stateMachineArn=extract_text_state_machine_arn,
                    input=json.dumps(workflow_input),
                )

                logger.info(
                    f"Started extract text workflow execution: {response['executionArn']}"
                )

            elif extension in [".mp4", ".mov", ".avi", ".mkv", ".mp3"]:
                # Invoke the transcribe media workflow for video files
                logger.info(f"Invoking transcribe media workflow for {object_key}")

                filename = os.path.basename(object_key)

                # Prepare input for the Step Functions workflow
                workflow_input = {
                    "bucket_name": bucket_name,
                    "filename": filename,
                    "object_key": object_key,
                    "file_extension": extension,
                }

                # Start the Step Functions execution
                response = sfn_client.start_execution(
                    stateMachineArn=transcribe_media_state_machine_arn,
                    input=json.dumps(workflow_input),
                )

                logger.info(
                    f"Started transcribe media workflow execution: {response['executionArn']}"
                )
            else:
                logger.warning(f"Unsupported file type: {extension} for {object_key}")

        except Exception as e:
            logger.exception(f"Error processing SQS message: {str(e)}")