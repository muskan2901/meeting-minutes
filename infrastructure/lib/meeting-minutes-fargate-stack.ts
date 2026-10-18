import { Stack, StackProps, Duration, RemovalPolicy, CfnOutput } from "aws-cdk-lib";
import { Construct } from "constructs";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
import * as elbv2 from "aws-cdk-lib/aws-elasticloadbalancingv2";
import * as ecrAssets from "aws-cdk-lib/aws-ecr-assets";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as cloudwatchActions from "aws-cdk-lib/aws-cloudwatch-actions";
import * as sns from "aws-cdk-lib/aws-sns";
import * as path from "path";

export class MeetingMinutesFargateStack extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);



    // Create S3 buckets for input and output
    const inputBucket = new s3.Bucket(this, "MeetingInputsBucket", {
      bucketName: `meeting-minutes-inputs-${this.account}-${this.region}`,
      versioned: true,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    const outputsBucket = new s3.Bucket(this, "MeetingOutputsBucket", {
      bucketName: `meeting-minutes-outputs-${this.account}-${this.region}`,
      versioned: true,
      lifecycleRules: [
        {
          id: "DeleteOldVersions",
          expiration: Duration.days(90),
          noncurrentVersionExpiration: Duration.days(30),
        },
        {
          id: "CleanupJobStatus",
          prefix: "job-status/",
          expiration: Duration.days(30), // Clean up job status files after 30 days
        },
      ],
      cors: [
        {
          allowedMethods: [s3.HttpMethods.GET, s3.HttpMethods.PUT, s3.HttpMethods.POST],
          allowedOrigins: [
            "http://localhost:8501", // Streamlit local development
            "http://127.0.0.1:8501",
            // Add your production frontend domains here
          ],
          allowedHeaders: [
            "Content-Type",
            "Content-Length",
            "Authorization",
            "X-Amz-Date",
            "X-Amz-Security-Token",
            "x-amz-meta-*",
          ],
          exposedHeaders: ["ETag"],
          maxAge: 3600,
        },
      ],
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // Create a VPC for our Fargate service
    const vpc = new ec2.Vpc(this, "MeetingMinutesVpc", {
      maxAzs: 2,
      natGateways: 1,
      subnetConfiguration: [
        {
          cidrMask: 24,
          name: "Public",
          subnetType: ec2.SubnetType.PUBLIC,
        },
        {
          cidrMask: 24,
          name: "Private",
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
        },
      ],
    });

    // Create an ECS cluster
    const cluster = new ecs.Cluster(this, "MeetingMinutesCluster", {
      vpc,
      containerInsights: true,
    });

    // Create a log group for the container
    const logGroup = new logs.LogGroup(this, "MeetingMinutesServiceLogs", {
      logGroupName: "/ecs/meeting-minutes-service",
      retention: logs.RetentionDays.ONE_MONTH, // Increased retention for debugging
      removalPolicy: RemovalPolicy.DESTROY,
    });

    // Create a task execution role
    const executionRole = new iam.Role(this, "MeetingMinutesTaskExecutionRole", {
      assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
      managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName("service-role/AmazonECSTaskExecutionRolePolicy")],
    });

    // Create a task role with comprehensive permissions
    const taskRole = new iam.Role(this, "MeetingMinutesTaskRole", {
      assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
    });

    // Add permissions for Bedrock APIs
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "bedrock:ListFoundationModels",
          "bedrock:GetFoundationModel",
        ],
        resources: ["*"],
      }),
    );

    // Add permissions for Polly (text-to-speech)
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["polly:SynthesizeSpeech", "polly:DescribeVoices"],
        resources: ["*"],
      }),
    );

    // Add permissions for S3 buckets (including job status management)
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket", "s3:GetBucketLocation"],
        resources: [
          inputBucket.bucketArn,
          `${inputBucket.bucketArn}/*`,
          outputsBucket.bucketArn,
          `${outputsBucket.bucketArn}/*`,
        ],
      }),
    );

    // Add permission for S3 credential validation
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["s3:ListAllMyBuckets"],
        resources: ["*"],
      }),
    );

    // Add permissions for CloudWatch Logs
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams",
        ],
        resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:/ecs/meeting-minutes-service*`],
      }),
    );





    // Create a task definition with increased resources for AI processing
    const taskDefinition = new ecs.FargateTaskDefinition(this, "MeetingMinutesTaskDefinition", {
      memoryLimitMiB: 2048, // Increased memory for AI processing
      cpu: 1024, // Increased CPU for AI processing
      executionRole,
      taskRole,
      runtimePlatform: {
        cpuArchitecture: ecs.CpuArchitecture.X86_64,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
      },
    });



    // Create Docker image asset
    const dockerAsset = new ecrAssets.DockerImageAsset(this, "MeetingMinutesImage", {
      directory: path.join(__dirname, "../../"),
      file: "docker/Dockerfile", // Your Dockerfile path
      platform: ecrAssets.Platform.LINUX_AMD64,
      exclude: ["infrastructure", ".git", "*.md", ".venv", "__pycache__", "*.pyc"],
    });

    // Add container to the task definition
    taskDefinition.addContainer("MeetingMinutesContainer", {
      image: ecs.ContainerImage.fromDockerImageAsset(dockerAsset),
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: "meeting-minutes-service",
        logGroup,
      }),
      environment: {
        LOG_LEVEL: "INFO",
        INPUT_BUCKET_NAME: inputBucket.bucketName,
        OUTPUT_BUCKET_NAME: outputsBucket.bucketName,
        AWS_DEFAULT_REGION: this.region,
        PYTHONUNBUFFERED: "1", // Important for real-time logging
      },
      portMappings: [
        {
          containerPort: 8000,
          protocol: ecs.Protocol.TCP,
        },
      ],
      healthCheck: {
        command: ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"],
        interval: Duration.seconds(30),
        timeout: Duration.seconds(10),
        retries: 3,
        startPeriod: Duration.seconds(180), // Longer start period for Strands initialization
      },
    });

    // Create security group for the service
    const serviceSecurityGroup = new ec2.SecurityGroup(this, "MeetingMinutesServiceSG", {
      vpc,
      description: "Security group for Meeting Minutes Fargate Service",
      allowAllOutbound: true,
    });

    // Create security group for the load balancer
    const albSecurityGroup = new ec2.SecurityGroup(this, "MeetingMinutesALBSG", {
      vpc,
      description: "Security group for Meeting Minutes ALB",
      allowAllOutbound: true,
    });

    // Allow HTTP traffic to ALB
    albSecurityGroup.addIngressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(80), "Allow HTTP traffic from anywhere");

    // Allow HTTPS traffic to ALB
    albSecurityGroup.addIngressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(443), "Allow HTTPS traffic from anywhere");

    // Allow traffic from ALB to service
    serviceSecurityGroup.addIngressRule(albSecurityGroup, ec2.Port.tcp(8000), "Allow traffic from ALB to service");

    // Create a Fargate service with proper configuration
    const service = new ecs.FargateService(this, "MeetingMinutesService", {
      cluster,
      taskDefinition,
      desiredCount: 1,
      assignPublicIp: false,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      securityGroups: [serviceSecurityGroup],
      circuitBreaker: {
        rollback: true,
      },
      minHealthyPercent: 0, // Allow zero instances during deployment
      maxHealthyPercent: 200,
      healthCheckGracePeriod: Duration.seconds(300), // Extended grace period for AI processing
    });

    // Create an Application Load Balancer
    const lb = new elbv2.ApplicationLoadBalancer(this, "MeetingMinutesLB", {
      vpc,
      internetFacing: true,
      securityGroup: albSecurityGroup,
    });

    // Create a listener
    const listener = lb.addListener("MeetingMinutesListener", {
      port: 80,
      protocol: elbv2.ApplicationProtocol.HTTP,
    });

    // Add target group with extended timeout for long-running AI processing
    const targetGroup = listener.addTargets("MeetingMinutesTargets", {
      port: 8000,
      protocol: elbv2.ApplicationProtocol.HTTP,
      targets: [service],
      healthCheck: {
        path: "/health",
        interval: Duration.seconds(60),
        timeout: Duration.seconds(10),
        healthyHttpCodes: "200",
        unhealthyThresholdCount: 3,
        healthyThresholdCount: 2,
      },
      deregistrationDelay: Duration.seconds(60),
      targetGroupName: "meeting-minutes-tg",
    });

    // Configure ALB timeout for long-running requests (AI processing can take time)
    const cfnTargetGroup = targetGroup.node.defaultChild as elbv2.CfnTargetGroup;
    cfnTargetGroup.addPropertyOverride("TargetGroupAttributes", [
      {
        Key: "deregistration_delay.timeout_seconds",
        Value: "60",
      },
      {
        Key: "load_balancing.algorithm.type",
        Value: "least_outstanding_requests",
      },
    ]);

    // Set ALB idle timeout to 15 minutes for long AI processing requests
    const cfnLoadBalancer = lb.node.defaultChild as elbv2.CfnLoadBalancer;
    cfnLoadBalancer.addPropertyOverride("LoadBalancerAttributes", [
      {
        Key: "idle_timeout.timeout_seconds",
        Value: "900", // 15 minutes
      },
    ]);

    // Auto Scaling configuration
    const scaling = service.autoScaleTaskCount({
      minCapacity: 1,
      maxCapacity: 3, // Reasonable max for AI processing workload
    });

    // Scale based on CPU utilization
    scaling.scaleOnCpuUtilization("CpuScaling", {
      targetUtilizationPercent: 70, // Scale out at 70% CPU
      scaleInCooldown: Duration.minutes(10),
      scaleOutCooldown: Duration.minutes(5),
    });

    // Scale based on memory utilization
    scaling.scaleOnMemoryUtilization("MemoryScaling", {
      targetUtilizationPercent: 70,
      scaleInCooldown: Duration.minutes(10),
      scaleOutCooldown: Duration.minutes(5),
    });

    // Create SNS topic for alerts with SSL enforcement
    const alertTopic = new sns.Topic(this, "MeetingMinutesAlerts", {
      displayName: "Meeting Minutes Service Alerts",
      enforceSSL: true
    });

    // Add explicit policy to allow CloudWatch to publish to SNS topic
    alertTopic.addToResourcePolicy(new iam.PolicyStatement({
      sid: "AllowCloudWatchAlarmsToPublish",
      effect: iam.Effect.ALLOW,
      principals: [new iam.ServicePrincipal("cloudwatch.amazonaws.com")],
      actions: ["sns:Publish"],
      resources: [alertTopic.topicArn],
      conditions: {
        StringEquals: {
          "aws:SourceAccount": this.account
        }
      }
    }));

    // Create the alarm with explicit action
    const highCpuAlarm = new cloudwatch.Alarm(this, "HighCpuAlarm", {
      alarmName: "MeetingMinutes-HighCPU-Alert",
      alarmDescription: "CPU usage above 65% - early warning before auto-scaling at 70%",
      metric: new cloudwatch.Metric({
        namespace: "AWS/ECS",
        metricName: "CPUUtilization",
        dimensionsMap: {
          ServiceName: service.serviceName,
          ClusterName: cluster.clusterName,
        },
        statistic: "Average",
        period: Duration.minutes(1),
      }),
      threshold: 65,
      evaluationPeriods: 3,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // Add the SNS action to the alarm
    highCpuAlarm.addAlarmAction(new cloudwatchActions.SnsAction(alertTopic));

    // Optional: Add additional alarms for comprehensive monitoring
    const highMemoryAlarm = new cloudwatch.Alarm(this, "HighMemoryAlarm", {
      alarmName: "MeetingMinutes-HighMemory-Alert", 
      alarmDescription: "Memory usage above 80%",
      metric: new cloudwatch.Metric({
        namespace: "AWS/ECS",
        metricName: "MemoryUtilization",
        dimensionsMap: {
          ServiceName: service.serviceName,
          ClusterName: cluster.clusterName,
        },
        statistic: "Average",
        period: Duration.minutes(1),
      }),
      threshold: 80,
      evaluationPeriods: 3,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    highMemoryAlarm.addAlarmAction(new cloudwatchActions.SnsAction(alertTopic));

    // Service unhealthy alarm
    const serviceUnhealthyAlarm = new cloudwatch.Alarm(this, "ServiceUnhealthyAlarm", {
      alarmName: "MeetingMinutes-Service-Unhealthy",
      alarmDescription: "Service is unhealthy - failed health checks",
      metric: new cloudwatch.Metric({
        namespace: "AWS/ApplicationELB",
        metricName: "UnHealthyHostCount",
        dimensionsMap: {
          TargetGroup: targetGroup.targetGroupFullName,
          LoadBalancer: lb.loadBalancerFullName,
        },
        statistic: "Maximum",
        period: Duration.minutes(1),
      }),
      threshold: 1,
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
    });

    serviceUnhealthyAlarm.addAlarmAction(new cloudwatchActions.SnsAction(alertTopic));

    // Outputs
    new CfnOutput(this, "LoadBalancerDNS", {
      value: lb.loadBalancerDnsName,
      description: "DNS name of the load balancer",
      exportName: "MeetingMinutesServiceEndpoint",
    });

    new CfnOutput(this, "InputBucketName", {
      value: inputBucket.bucketName,
      description: "S3 bucket for meeting inputs",
      exportName: "MeetingMinutesInputsBucket",
    });

    new CfnOutput(this, "OutputBucketName", {
      value: outputsBucket.bucketName,
      description: "S3 bucket for meeting outputs and job status",
      exportName: "MeetingMinutesOutputsBucket",
    });

    new CfnOutput(this, "ServiceURL", {
      value: `http://${lb.loadBalancerDnsName}`,
      description: "Complete service URL",
    });

    new CfnOutput(this, "HealthCheckURL", {
      value: `http://${lb.loadBalancerDnsName}/health`,
      description: "Health check endpoint",
    });

    new CfnOutput(this, "ProcessMeetingURL", {
      value: `http://${lb.loadBalancerDnsName}/process-s3-meeting`,
      description: "Meeting processing endpoint",
    });

    new CfnOutput(this, "JobStatusURL", {
      value: `http://${lb.loadBalancerDnsName}/job-status/{job_id}`,
      description: "Job status checking endpoint",
    });

    new CfnOutput(this, "ListJobsURL", {
      value: `http://${lb.loadBalancerDnsName}/list-jobs`,
      description: "List all jobs endpoint",
    });

    new CfnOutput(this, "UsageInstructions", {
      value: [
        "1. POST meeting content to /process-s3-meeting",
        "2. Get job_id from response",
        "3. Check status at /job-status/{job_id}",
        "4. Download files when status is 'completed'",
      ].join(" | "),
      description: "API usage instructions",
    });

    // Output SNS topic ARN for email subscription
    new CfnOutput(this, "AlertTopicArn", {
      value: alertTopic.topicArn,
      description: "SNS topic ARN - subscribe your email: aws sns subscribe --topic-arn [this-arn] --protocol email --notification-endpoint your-email@example.com",
    });
  }
}
