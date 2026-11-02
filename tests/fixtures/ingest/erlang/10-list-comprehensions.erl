%% List comprehensions and filters.
-module(demo_lc).
-export([pairs/2, evens/1, pythagorean/1]).

pairs(Xs, Ys) ->
    [{X, Y} || X <- Xs, Y <- Ys].

evens(Xs) ->
    [X || X <- Xs, X rem 2 =:= 0].

pythagorean(N) ->
    [{A, B, C} || A <- lists:seq(1, N),
                  B <- lists:seq(A, N),
                  C <- lists:seq(B, N),
                  A * A + B * B =:= C * C].
