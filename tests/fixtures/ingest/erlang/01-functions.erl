%% Basic exported functions with multiple clauses.
-module(demo_functions).
-export([add/2, greet/1, greet/2, clamp/3, variadic/1]).

add(A, B) ->
    A + B.

greet(Name) ->
    greet(Name, "hello").

greet(Name, Greeting) ->
    Greeting ++ ", " ++ Name.

clamp(Value, Low, High) when Value < Low ->
    Low;
clamp(Value, _Low, High) when Value > High ->
    High;
clamp(Value, _Low, _High) ->
    Value.

variadic(Args) when is_list(Args) ->
    length(Args).
