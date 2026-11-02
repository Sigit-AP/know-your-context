%% -import attributes and remote calls.
-module(demo_imports).
-import(lists, [map/2, reverse/1]).
-import(string, [uppercase/1]).
-export([run/1]).

run(Values) ->
    uppercase(lists:concat(reverse(Values))).
