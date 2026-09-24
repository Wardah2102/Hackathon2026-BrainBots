"""DB Schema AI — a pluggable NL->SQL agent that adapts to any application's database.

Subpackages:
- introspection : read a database's structure and (optionally) AI-enrich it
- linking       : select the tables relevant to a question (schema linking)
- agent         : the agentic NL->SQL orchestrator, models and domain packs
- execution     : read-only SQL validation and execution
- llm           : model-agnostic Azure OpenAI client layer
"""
