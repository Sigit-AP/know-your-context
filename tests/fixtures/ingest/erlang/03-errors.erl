%% try/catch/after, throw/error/exit.
-module(demo_errors).
-export([read_config/2, read_safe/2]).

read_config(Map, Key) ->
    case maps:find(Key, Map) of
        {ok, Value} -> Value;
        error -> throw({missing_config_key, Key})
    end.

read_safe(Map, Key) ->
    try read_config(Map, Key) of
        Value -> {ok, Value}
    catch
        throw:{missing_config_key, K} -> {error, {missing, K}};
        error:Reason -> {error, Reason}
    after
        ok
    end.
