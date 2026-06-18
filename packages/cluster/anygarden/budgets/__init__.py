"""Token-cost ledger + invocation-block gate (#453, reliability Wave 1d).

See :mod:`anygarden.budgets.ledger` for the window SUM over the measured
``LLMGatewayUsage`` stream and the hard-stop evaluation the reverse
proxy consults before each upstream LLM call.
"""
