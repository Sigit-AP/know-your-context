%% Anonymous funs and higher-order functions.
-module(demo_funs).
-export([make_counter/1, apply_twice/2, compose/2]).

make_counter(Start) ->
    fun(Amount) -> Start + Amount end.

apply_twice(Fn, X) ->
    Fn(Fn(X)).

compose(F, G) ->
    fun(X) -> F(G(X)) end.
