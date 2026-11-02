%% spawn, message passing and receive.
-module(demo_processes).
-export([echo/0, fanout/2]).

echo() ->
    receive
        {From, Msg} ->
            From ! {self(), Msg},
            echo()
    end.

fanout(Pids, Msg) ->
    [Pid ! {self(), Msg} || Pid <- Pids],
    length(Pids).

spawn_echo() ->
    spawn(?MODULE, echo, []).
