-- Function applications parking unresolved refs.
module Demo.Calls where

helper :: Int -> Int
helper value = value * 2

run :: Int -> Int
run seed = helper (helper seed)
