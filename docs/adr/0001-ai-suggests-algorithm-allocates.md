# AI suggests, the user confirms, a fixed algorithm allocates

The Plan splits Income across Obligations and Goals. An LLM may only suggest a Goal's Importance and Dependencies, and the user confirms them. The money split itself is a deterministic algorithm. We rejected letting the LLM do the allocation because its output can differ between runs, can't be explained or tested exactly, and costs an API call each time. The Plan must also work when the LLM is unavailable.
