import json

from aws_cdk import (
    Stack,
    aws_lambda,
    CfnOutput,
    Duration,
    aws_s3 as s3,
    aws_sqs as sqs,
    aws_stepfunctions as sfn,
    aws_iam as iam,
    aws_s3,
    aws_s3_notifications,
    RemovalPolicy,
)
from aws_cdk.aws_lambda import Tracing
from aws_cdk.aws_lambda_event_sources import SqsEventSource
from aws_cdk.aws_lambda_python_alpha import PythonFunction
from aws_cdk.aws_s3 import NotificationKeyFilter
from cdklabs.generative_ai_cdk_constructs.bedrock import (
    VectorKnowledgeBase,
    BedrockFoundationModel,
    CustomDataSource,
    ChunkingStrategy,
    DataDeletionPolicy,
)
from cdklabs.generative_ai_cdk_constructs.pinecone import PineconeVectorStore
from constructs import Construct


class MultiModalStrandsAgentStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        ## s3 bucket
        multi_modal_bucket = s3.Bucket(
            self,
            "multi-modal-rag-bucket",
            versioned=False,
            removal_policy=RemovalPolicy.DESTROY,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        # Lambda functions
        upload_processor_function = PythonFunction(
            self,
            "UploadProcessorFunction",
            runtime=aws_lambda.Runtime.PYTHON_3_11,
            entry="./lambda_fns",
            index="upload_processor.py",
            handler="lambda_handler",
            memory_size=512,
            tracing=Tracing.ACTIVE,
            timeout=Duration.minutes(5),
        )
        # Lambda functions
        queue_processor_function = PythonFunction(
            self,
            "QueueProcessorFunction",
            runtime=aws_lambda.Runtime.PYTHON_3_11,
            entry="./lambda_fns",
            index="queue_processor.py",
            handler="lambda_handler",
            tracing=Tracing.ACTIVE,
            memory_size=1024,
            timeout=Duration.minutes(5),
        )

        # Lambda functions
        save_transcribed_text_function = PythonFunction(
            self,
            "SaveTranscribedTextFunction",
            runtime=aws_lambda.Runtime.PYTHON_3_11,
            entry="./lambda_fns",
            index="save_transcribed_text_function.py",
            handler="lambda_handler",
            memory_size=1024,
            tracing=Tracing.ACTIVE,
            timeout=Duration.minutes(10),
        )

        # Lambda functions
        save_textract_text_function = PythonFunction(
            self,
            "SaveTextractTextFunction",
            runtime=aws_lambda.Runtime.PYTHON_3_11,
            entry="./lambda_fns",
            index="save_textract_text_function.py",
            handler="lambda_handler",
            memory_size=1024,
            tracing=Tracing.ACTIVE,
            timeout=Duration.minutes(10),
        )
        # Create DLQ to catch unprocessed or failed messages
        dlq = sqs.Queue(
            self,
            "MultiModalDLQ",
            queue_name="multi-modal-agent-dlq",
            retention_period=Duration.days(14),
        )  # Retain messages for 14 days
        # Create Queue and attached DLQ
        self.sqs_queue = sqs.Queue(
            self,
            "MultiModalQueue",
            visibility_timeout=Duration.minutes(50),
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=3,
                queue=dlq,  # Retry 3 times before sending to DLQ
            ),
        )
        # read more here https://docs.powertools.aws.dev/lambda/python/latest/utilities/batch/
        # Add the event source (SQS trigger)
        queue_processor_function.add_event_source(
            SqsEventSource(
                self.sqs_queue, batch_size=10, report_batch_item_failures=True
            )
        )

        upload_processor_function.add_environment("QUEUE", self.sqs_queue.queue_name)
        upload_processor_function.add_environment(
            "BUCKET", multi_modal_bucket.bucket_name
        )

        # Load the PDF Processor ASL definition from the JSON file
        with open("./workflows/textract_pdf_workflow.asl.json", "r") as file:
            extract_text_sm_definition = json.load(file)

        # Load the Audio/Video ASL definition from the JSON file
        with open("./workflows/transcribe_media_workflow.asl.json", "r") as file:
            state_machine_definition = json.load(file)

        # Create the Step Functions state machine using the ASL definition
        audio_video_state_machine = sfn.StateMachine(
            self,
            "ProcessAudioVideoStateMachine",
            definition_body=sfn.DefinitionBody.from_string(
                json.dumps(state_machine_definition)
            ),
            definition_substitutions={
                "FUNCTION_ARN": save_transcribed_text_function.function_arn,
            },
            # Use definition_body
            state_machine_type=sfn.StateMachineType.STANDARD,
        )
        save_transcribed_text_function.grant_invoke(audio_video_state_machine)

        # Create the Step Functions state machine for PDF Processor
        pdf_processor_state_machine = sfn.StateMachine(
            self,
            "ProcessPDFStateMachine",
            definition_body=sfn.DefinitionBody.from_string(
                json.dumps(extract_text_sm_definition)
            ),
            definition_substitutions={
                "FUNCTION_ARN": save_textract_text_function.function_arn,
            },
            # Use definition_body
            state_machine_type=sfn.StateMachineType.STANDARD,
        )

        save_textract_text_function.grant_invoke(pdf_processor_state_machine)

        # Grant the state machine permissions to use Textract
        audio_video_state_machine.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "transcribe:StartTranscriptionJob",
                    "transcribe:GetTranscriptionJob",
                ],
                resources=["*"],  # Textract does not support resource-level permissions
            )
        )

        # Grant the state machine permissions to use Textract
        pdf_processor_state_machine.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "textract:StartDocumentTextDetection",
                    "textract:GetDocumentTextDetection",
                    "textract:DetectDocumentText",
                ],
                resources=["*"],  # Textract does not support resource-level permissions
            )
        )

        multi_modal_bucket.grant_read_write(pdf_processor_state_machine)
        multi_modal_bucket.grant_read_write(audio_video_state_machine)
        multi_modal_bucket.grant_read_write(queue_processor_function)

        pdf_processor_state_machine.grant_start_execution(queue_processor_function)
        audio_video_state_machine.grant_start_execution(queue_processor_function)

        queue_processor_function.add_environment(
            "EXTRACT_TEXT_STATE_MACHINE_ARN",
            pdf_processor_state_machine.state_machine_arn,
        )
        queue_processor_function.add_environment(
            "TRANSCRIBE_MEDIA_STATE_MACHINE_ARN",
            audio_video_state_machine.state_machine_arn,
        )
        save_transcribed_text_function.add_environment("BYPASS_TOOL_CONSENT", "True")
        save_textract_text_function.add_environment("BYPASS_TOOL_CONSENT", "True")

        # Step 3: Grant the Lambda function permissions to read from the S3 bucket
        multi_modal_bucket.grant_read_write(upload_processor_function)
        # Add an S3 event notification to trigger the Lambda function

        # Step 5: Add an S3 event trigger to invoke the Lambda function
        notification = aws_s3_notifications.LambdaDestination(upload_processor_function)
        # grant send message permissions to upload lambda functions
        self.sqs_queue.grant_send_messages(upload_processor_function)

        self.sqs_queue.grant_consume_messages(queue_processor_function)

        multi_modal_bucket.grant_read(save_transcribed_text_function)

        multi_modal_bucket.add_event_notification(
            aws_s3.EventType.OBJECT_CREATED,
            notification,
            NotificationKeyFilter(prefix="uploads/"),
        )

        queue_processor_function.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:ListDataSources",
                    "bedrock:StartIngestionJob",
                    "bedrock:GetIngestionJob",
                    "bedrock:ListIngestionJobs",
                    "bedrock:IngestKnowledgeBaseDocuments",
                    "bedrock:AssociateThirdPartyKnowledgeBase",
                ],
                resources=["*"],  # Textract does not support resource-level permissions
            )
        )
        save_textract_text_function.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:ListDataSources",
                    "bedrock:StartIngestionJob",
                    "bedrock:GetIngestionJob",
                    "bedrock:ListIngestionJobs",
                    "bedrock:IngestKnowledgeBaseDocuments",
                    "bedrock:AssociateThirdPartyKnowledgeBase",
                ],
                resources=["*"],  # Textract does not support resource-level permissions
            )
        )
        save_transcribed_text_function.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:ListDataSources",
                    "bedrock:StartIngestionJob",
                    "bedrock:GetIngestionJob",
                    "bedrock:ListIngestionJobs",
                    "bedrock:IngestKnowledgeBaseDocuments",
                    "bedrock:AssociateThirdPartyKnowledgeBase",
                ],
                resources=["*"],  # Textract does not support resource-level permissions
            )
        )
        # create a pinecone cdk resource

        pinecone_vec = PineconeVectorStore(
            connection_string="https://m7-9789jkj.io",
            credentials_secret_arn="arn::-khjjjkj4JvqP",
            text_field="text",
            metadata_field="metadata",
        )

        agent_knowledge_base = VectorKnowledgeBase(
            self,
            "ContentAgentKnowledgeBase",
            name="ContentAgentKnowledgeBase",
            vector_store=pinecone_vec,
            embeddings_model=BedrockFoundationModel.TITAN_EMBED_TEXT_V2_1024,
            instruction="Use this knowledge base to summarize,generate QA and flash cards about workshops "
            + "It contains some workshops gotten from educloud.academy.",
        )

        CustomDataSource(
            self,
            "KnowledgeBaseCustomDatasource",
            data_source_name="MultiModalKBStrandsAgentDatasource",
            knowledge_base=agent_knowledge_base,
            chunking_strategy=ChunkingStrategy.FIXED_SIZE,
            data_deletion_policy=DataDeletionPolicy.RETAIN,
        )

        save_transcribed_text_function.add_environment(
            "STRANDS_KNOWLEDGE_BASE_ID", agent_knowledge_base.knowledge_base_id
        )
        save_textract_text_function.add_environment(
            "STRANDS_KNOWLEDGE_BASE_ID", agent_knowledge_base.knowledge_base_id
        )
        queue_processor_function.add_environment(
            "STRANDS_KNOWLEDGE_BASE_ID", agent_knowledge_base.knowledge_base_id
        )
        queue_processor_function.add_environment("BYPASS_TOOL_CONSENT", "True")

        # Output for S3 bucket
        CfnOutput(self, "MultiModalBucketName", value=multi_modal_bucket.bucket_name)
        CfnOutput(self, "MultiModalBucketArn", value=multi_modal_bucket.bucket_arn)

        # Outputs for Lambda functions
        CfnOutput(
            self,
            "UploadProcessorFunctionArn",
            value=upload_processor_function.function_arn,
        )
        CfnOutput(
            self,
            "QueueProcessorFunctionArn",
            value=queue_processor_function.function_arn,
        )
        CfnOutput(
            self,
            "ExtractTextFunctionArn",
            value=save_transcribed_text_function.function_arn,
        )

        # Outputs for State Machines
        CfnOutput(
            self,
            "PDFProcessorStateMachineArn",
            value=pdf_processor_state_machine.state_machine_arn,
        )
        CfnOutput(
            self,
            "AudioVideoStateMachineArn",
            value=audio_video_state_machine.state_machine_arn,
        )

        # Output for SQS Queue
        CfnOutput(self, "SQSQueueUrl", value=self.sqs_queue.queue_url)
        CfnOutput(self, "SQSQueueArn", value=self.sqs_queue.queue_arn)
        CfnOutput(self, "DLQQueueUrl", value=dlq.queue_url)
        CfnOutput(self, "DLQQueueArn", value=dlq.queue_arn)

        # Output for Knowledge Base ID
        CfnOutput(self, "KnowledgeBaseId", value=agent_knowledge_base.knowledge_base_id)