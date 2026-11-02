%% Map update syntax and iterators.
-module(demo_maps).
-export([merge_defaults/1, keys_sorted/1]).

defaults() ->
    #{timeout => 30, retries => 3}.

merge_defaults(Overrides) ->
    maps:merge(defaults(), Overrides).

keys_sorted(Map) ->
    lists:sort(maps:keys(Map)).
