-- Pattern matching: constructors, guards, view-ish case.
module Demo.Patterns where

data Tree a = Leaf a | Branch (Tree a) (Tree a)

depth :: Tree a -> Int
depth (Leaf _) = 1
depth (Branch l r) = 1 + max (depth l) (depth r)

classify :: Int -> String
classify n = case n of
  0 -> "zero"
  1 -> "one"
  _ | n < 0 -> "negative"
    | otherwise -> "many"
