One thing while we're here — we need to make sure the cherry-pick implementation never blocks on stdin prompts during conflict resolution. The visual conflict UI has to be the only path; if the implementation ever asks the user to resolve a conflict via terminal input, that's a regression we have to prevent.

Worth tracking alongside the cherry-pick work so it doesn't get lost in conversation.
