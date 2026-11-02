%% gen_server behaviour skeleton.
-module(demo_counter).
-behaviour(gen_server).
-export([start_link/0, bump/0]).
-export([init/1, handle_call/3, handle_cast/2]).

start_link() ->
    gen_server:start_link({local, ?MODULE}, ?MODULE, [], []).

bump() ->
    gen_server:call(?MODULE, bump).

init([]) ->
    {ok, 0}.

handle_call(bump, _From, State) ->
    {reply, State + 1, State + 1}.

handle_cast(_Msg, State) ->
    {noreply, State}.
