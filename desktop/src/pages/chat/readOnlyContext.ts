/**
 * readOnlyContext — whether the message being rendered is a read-only preview.
 *
 * AssistantMessageView provides it from its `readOnly` prop (History preview), so
 * leaf controls deep in the turn (FileChip, the Produced tiles) go inert without
 * the flag being threaded through every component in between. Default false: a
 * live chat.
 *
 * @exports ReadOnlyContext
 */
import { createContext } from 'react';

export const ReadOnlyContext = createContext(false);
