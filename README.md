# multi-modal-rag-strands-agent

# Building a Multi-Modal RAG pipeline with AWS CDK,Strands,AWS Stepfunctions

## References
https://docs.aws.amazon.com/lambda/latest/dg/with-sqs.html#events-sqs-queueconfig


## Prerequisites
- AWS CLI
- AWS Bedrock Access AWS Credentials
- Strands Agent CLI
- Docker
- python >=3.11
### Create CDK PROJECT
```bash
cdk init app --language=python
```

### Import And Configure Dependencies

```txt
aws-cdk.aws-lambda-python-alpha==2.175.1a0
cdklabs.generative-ai-cdk-constructs==0.1.309
pre-commit==4.1.0
ruff==0.9.1
```

lambda_fns

```txt
aws-lambda-powertools[tracer]
boto3>=1.34.0
strands-agents>=0.1.0
strands-agents-tools>=0.1.0


```



### Create S3 bucket
- done

### Create 4 Lambda Function Skeletons
- done

- `upload_processor.py` ---> `uploads/`(s3)
- `queue_processor.py` --> polls from the sqs queue
- `save_textract_text_function.py` --> saves text extracted from pdf files
- `save_transcribed_text_function.py`--> saves transcribed text from audio/video



### Create 4 Lambda function resources
- done


### Create SQS and DLQ
- done


### Grant Appropriate Permissions to S3/Lambda/SQS
 - done


### Create PDF Processor State Machine Resource
- done

### Create PDF Statemachine Workflow
- done

### Grant Appropriate Permissions to PDF State Machine
- done

### Create Audio/Video Processor State Machine Resource
- done

### Create Audio/Video Statemachine
- done

### Grant Appropriate Permissions to Audio/Video State machine
- done

### Update Lambda functions(extract text) with AI Agent With Strands

### Create AWS Bedrock Knowledge base with Custom Datasource and Pinecone
- done
### Grant extract text lambda function to save to KB
- done

### Conclusion
- create an endpoint that enables users to query this KB.
