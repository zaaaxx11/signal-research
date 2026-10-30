"""Operations the deployment runs against itself.

The first tenant is :mod:`app.ops.alerting`: a decision engine that turns
observed facts into events, and :mod:`app.ops.sinks`, the transports that carry
those events. The split is deliberate. The engine knows what is wrong and
whether it already said so; it does not know whether "saying so" means a file,
Telegram, an email or a webhook. Adding a platform later is a new class in
``sinks`` and a new scheme in a spec string, never an edit to a transition rule.
"""
