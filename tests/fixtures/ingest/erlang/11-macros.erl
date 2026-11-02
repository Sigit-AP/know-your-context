%% Macros and conditional compilation.
-module(demo_macros).
-define(LIMIT, 100).
-define(LOG(Msg), io:format("~s~n", [Msg])).
-export([limit/0, report/1]).

-ifdef(DEBUG).
-define(TRACE(X), io:format("trace ~p~n", [X])).
-else.
-define(TRACE(X), ok).
-endif.

limit() ->
    ?LIMIT.

report(Value) ->
    ?TRACE(Value),
    ?LOG("done").
