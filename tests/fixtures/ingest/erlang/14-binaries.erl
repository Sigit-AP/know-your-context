%% Binary construction and bit syntax.
-module(demo_binaries).
-export([encode_header/2, decode_header/1]).

encode_header(Type, Length) ->
    <<Type:8, Length:16/big-unsigned-integer>>.

decode_header(<<Type:8, Length:16/big, Rest/binary>>) ->
    {ok, Type, Length, Rest};
decode_header(_) ->
    {error, truncated}.
