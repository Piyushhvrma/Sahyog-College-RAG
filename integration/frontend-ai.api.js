// frontend/src/api/ai.api.js   — REPLACEMENT-STYLE PATCH (keep your existing import line + existing exports!)
//
// ADAPT: I could not read your current file. Keep whatever it already does for the axios import and for
// General chat; the only change is that sendChat now accepts a `mode` and returns sources.
// If your current function is named differently (e.g. sendAIMessage / chatWithAI), keep THAT name and just
// add `mode` to the request body.

import api from './index'; // ADAPT: keep your file's existing import line

/**
 * @param {string} message
 * @param {{role:'user'|'assistant', content:string}[]} history  recent turns (last ~6)
 * @param {'general'|'college'} mode
 * @returns {Promise<{reply:string, sources?:Array, mode?:string, grounded?:boolean}>}
 */
export const sendChat = async (message, history = [], mode = 'general') => {
  const res = await api.post(
    '/ai/chat',
    { message, history: history.slice(-6), mode },
    { timeout: 60000 } // college mode = retrieval + generation; allow cold starts
  );
  const data = res.data || {};
  return {
    ...data,
    // ADAPT: if your backend's reply key isn't "reply", map it here so the page keeps working
    reply: data.reply ?? data.message ?? data.response ?? '',
    sources: Array.isArray(data.sources) ? data.sources : [],
  };
};
