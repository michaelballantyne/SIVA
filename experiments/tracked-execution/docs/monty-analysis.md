# Monty Opaque Object Support — Feasibility Analysis

Date: 2026-04-10

## Summary

Pydantic Monty (v0.0.10) currently only supports dataclasses and basic
Python types as return values from external functions. Numpy arrays and
PyVista meshes fail with `TypeError: Cannot convert <type> to Monty value`.

After analyzing the Monty Rust source code, adding opaque object support
is **feasible and moderate effort** — the dataclass method dispatch
infrastructure already provides the blueprint.

## Where the Error Comes From

`crates/monty-python/src/convert.rs`, lines 137-145. The `py_to_monty()`
function is an explicit whitelist of supported types. When no match is
found, it raises `TypeError`.

## How Dataclass Method Dispatch Works (The Blueprint)

1. VM encounters `obj.method(args)` on a dataclass
2. `py_call_attr()` in `types/dataclass.rs` sees the method isn't in attrs
3. Returns `CallResult::MethodCall(method_name, args_with_self)`
4. VM yields `FrameExit::MethodCall` to the host
5. Host calls `getattr(py_self, method_name)(*args, **kwargs)`
6. Result converted back via `py_to_monty()` and resumed in VM

This exact pattern applies to opaque objects — just dispatch ALL attribute
access to the host instead of checking attrs first.

## What Would Need to Change

### Easy (a few days)
- Add `MontyObject::OpaqueObject { type_name, obj_id }` variant
- Add conversion paths in `py_to_monty()` / `monty_to_py()`
- Add heap type with `py_call_attr()` that dispatches to host
- Extend `dispatch_method_call()` to handle opaque objects

### Medium (add ~1 week)
- `__getitem__` support — needs new `FrameExit` variant or reuse MethodCall
  with `__getitem__` as the method name
- Operator support (`__gt__`, `__add__`, etc.) — similar pattern
- Whitelist/security parameter on the Monty API

### Hard (add another week)
- GC for the opaque object store (weak references, scoped lifetime)
- Snapshot serialization
- Iteration protocol (`__iter__`, `__next__`)

## Estimated Effort

- **MVP (methods + __getitem__):** ~1 week
- **Full operators:** ~2 weeks
- **Production quality:** ~3-4 weeks

## Key Files in Monty Source

| File | Purpose |
|------|---------|
| `crates/monty/src/object.rs:237-342` | MontyObject enum — add variant here |
| `crates/monty-python/src/convert.rs:26-146` | py_to_monty — where error originates |
| `crates/monty/src/types/dataclass.rs:267-306` | Dataclass method dispatch — the template |
| `crates/monty/src/bytecode/vm/mod.rs:250-329` | FrameExit enum — may need new variants |
| `crates/monty-python/src/external.rs:28-83` | Host-side method invocation |

## No Existing Issues/PRs

No existing discussion about opaque objects, foreign objects, or numpy/PyVista
integration on the Monty GitHub repo. Worth filing a feature request.

## Alternatives Within Current Monty

None work well:
- **Dynamically-generated dataclasses**: No __getitem__ support, brittle
- **Dict wrappers**: Breaks method calls, loses identity
- **Wrap everything as external functions**: Verbose, kills PyVista syntax

## Recommendation

File a feature request on pydantic/monty. In the meantime, build on CPython
restricted exec (which gives us the caching/reconciliation value now). Port
to Monty when opaque objects ship.
