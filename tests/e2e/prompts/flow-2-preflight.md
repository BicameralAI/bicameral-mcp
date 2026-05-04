Add a programmatic API for reordering commits — it takes an ordered list of commit SHAs and rewrites the branch history to match that order. Wire it so any UI surface can call it with a sorted list and apply the new order.

I'll handle the call-site cleanup separately.
