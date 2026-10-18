#!/usr/bin/env node
import { App } from "aws-cdk-lib";
import { MeetingMinutesFargateStack } from "../lib/meeting-minutes-fargate-stack";

const app = new App();

new MeetingMinutesFargateStack(app, "MeetingMinutesFargateStack", {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
  },
});
