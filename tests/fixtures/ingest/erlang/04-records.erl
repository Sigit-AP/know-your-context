%% Records and typespecs.
-module(demo_records).
-record(point, {x = 0 :: integer(), y = 0 :: integer()}).
-record(user, {name :: string(), tags = [] :: [string()]}).
-export([origin/0, move/3]).

-type point() :: #point{}.

-spec origin() -> point().
origin() ->
    #point{}.

move(#point{x = X, y = Y} = P, Dx, Dy) ->
    P#point{x = X + Dx, y = Y + Dy}.
