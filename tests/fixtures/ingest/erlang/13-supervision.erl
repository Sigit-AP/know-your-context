%% supervisor behaviour skeleton.
-module(demo_sup).
-behaviour(supervisor).
-export([start_link/0]).
-export([init/1]).

start_link() ->
    supervisor:start_link({local, ?MODULE}, ?MODULE, []).

init([]) ->
    Child = #{id => demo_counter,
              start => {demo_counter, start_link, []},
              restart => permanent,
              shutdown => 5000,
              type => worker},
    {ok, {#{strategy => one_for_one, intensity => 5, period => 10}, [Child]}}.
