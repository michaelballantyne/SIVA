# Human-Agent Coordination via MCP

The MCP has no way to push events to the agent, but tool responses can carry
coordination signals on the next agent action.

## Ideas to implement

**Spec file conflict detection in `set_pipeline`**: if the spec file's mtime is
newer than the last saved version, refuse the call and tell the agent to
`get_pipeline()` first. Mirrors how Claude Code gates writes on a read.

**Camera clobber warning in `set_camera`**: track whether the agent was the last
to set the camera. If the human has rotated the window since, warn before
overwriting. Harder since VTK doesn't distinguish who moved the camera —
would need to timestamp the agent's last `set_camera` call and compare against
a "camera last moved" signal, if that's even available.
