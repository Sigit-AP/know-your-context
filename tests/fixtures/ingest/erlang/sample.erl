-module(greeter).
-export([greet/1]).
-import(string, [uppercase/1]).

greet(Name) ->
    helper(Name).

helper(X) ->
    uppercase(X).
