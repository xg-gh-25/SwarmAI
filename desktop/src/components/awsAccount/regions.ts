/** The AWS regions offered as the main region (Settings → Swarm AWS Account, Hive-mode
 *  sign-in). A region set elsewhere that is not listed is shown as itself. */
export const AWS_REGION_OPTIONS = [
  { id: 'us-east-1', name: 'US East (N. Virginia)', description: 'us-east-1' },
  { id: 'us-west-2', name: 'US West (Oregon)', description: 'us-west-2' },
  { id: 'eu-west-1', name: 'EU (Ireland)', description: 'eu-west-1' },
  { id: 'eu-central-1', name: 'EU (Frankfurt)', description: 'eu-central-1' },
  { id: 'ap-northeast-1', name: 'Asia Pacific (Tokyo)', description: 'ap-northeast-1' },
  { id: 'ap-southeast-1', name: 'Asia Pacific (Singapore)', description: 'ap-southeast-1' },
];

export function regionOptions(current?: string | null) {
  return current && !AWS_REGION_OPTIONS.some((r) => r.id === current)
    ? [...AWS_REGION_OPTIONS, { id: current, name: current, description: current }]
    : AWS_REGION_OPTIONS;
}
