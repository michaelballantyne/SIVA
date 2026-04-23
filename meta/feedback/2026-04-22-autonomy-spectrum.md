# Autonomy Spectrum and Legibility

The current design implicitly supports a spectrum from fully human-directed to
fully autonomous exploration. At one end, the human specifies each step; at the
other, the agent continuously explores -- querying data, building views,
reading background literature, deciding what's interesting next -- without
human input until it has something worth showing.

The declarative spec is what makes the autonomous end of the spectrum
*legible*: the human can drop in at any point, read exactly what the agent
decided to build and why (from the conversation), redirect or approve, and step
back out. Without a readable shared artifact, autonomous operation would be a
black box.

## Future direction

A "continuous exploration" mode where the agent is explicitly instructed to
keep exploring until interrupted -- cycling through data queries, visualization
attempts, screenshot assessment, domain literature searches -- would be a
natural extension. The current tools already support this; it's just a prompting
pattern, not a new feature.

The interesting design question is whether the system should provide explicit
support for this mode: e.g. a way for the agent to flag "I found something
worth your attention" rather than requiring the human to check in periodically.
