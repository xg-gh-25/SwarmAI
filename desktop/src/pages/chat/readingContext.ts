/**
 * readingContext — whether the user is READING (scrolled away from the bottom) in this
 * tab, as TabView's scroll mode says (pages/chat/scrollFocus.ts). A live Worked group
 * folds the segment the agent moved on from only while the user FOLLOWs; while they
 * read, nothing folds under them.
 *
 * @exports ReadingContext
 */
import { createContext } from 'react';

/** True while the user reads (not following the bottom). Default: following. */
export const ReadingContext = createContext(false);
