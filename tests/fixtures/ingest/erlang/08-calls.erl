%% Calls parking unresolved refs.
-module(demo_calls).
-export([run/1]).

helper(Value) ->
    Value * 2.

run(Seed) ->
    Total = helper(Seed),
    helper(Total).
