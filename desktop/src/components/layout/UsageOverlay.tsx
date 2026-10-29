/**
 * UsageOverlay — the usage view as an overlay's content (System zone; also opened by
 * a click on the top bar's token figures). The view itself is components/usage/UsageView.
 */
import { UsageView, type UsageViewProps } from '../usage/UsageView';

export function UsageContent(props: UsageViewProps) {
  return <UsageView {...props} />;
}

export default UsageContent;
