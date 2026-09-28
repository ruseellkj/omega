"""omega_ai — Layer 1, the only code that knows a vendor exists.

One module per **wire format**, not per vendor: `openai.py` also reaches Groq,
Together, Ollama and vLLM, because they all speak Chat Completions.

Everything vendor-shaped stops here. Retries happen below the event boundary and
are invisible above it; stop reasons are normalised to three values, and an
ending that is none of them (a refusal, a content filter, a value no table
lists) is reported rather than called a finish; failures arrive as `error`
events rather than exceptions. Those promises are what let the
rest of the system be written as though only one provider existed.

The proof that it holds is in the git history: adding the second adapter changed
this package, its tests, and a few lines of provider selection in
`omega_coding/cli.py`. Nothing else moved.
"""
