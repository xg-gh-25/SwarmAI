import { createContext } from 'react';

/**
 * True while the markdown around it is still streaming. Parts that render
 * asynchronously read it to tell "not written yet" from "broken": a half-written
 * mermaid source fails to parse on every streamed chunk, which is not an error
 * until the stream ends.
 */
export const MarkdownLiveContext = createContext(false);
