# Stateless Wave Execution

A Migration Wave executes as a stateless, in-memory run from Pre-checks through the final Migration Report without persisting intermediate checkpoints to a local state file or database. If a Migration Unit fails at any stage (such as Pre-checks, GitHub Setup, Migration, or Post-checks due to Source Drift), it is marked failed for that run and recorded in the Migration Report rather than pausing or auto-looping, keeping orchestration deterministic and free of state-synchronization overhead.
