%% Rich pattern matching: binaries, maps, guards.
-module(demo_patterns).
-export([classify/1, greet_bin/1]).

classify({ok, #{status := 200}}) -> ok;
classify({ok, #{status := S}}) when S >= 400 -> {error, S};
classify({error, Reason}) -> {error, Reason};
classify(_) -> unknown.

greet_bin(<<Greeting:5/binary, Rest/binary>>) ->
    {Greeting, Rest};
greet_bin(Other) ->
    {<<>>, Other}.
