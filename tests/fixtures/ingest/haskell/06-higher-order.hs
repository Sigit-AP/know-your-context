-- Higher-order functions and composition.
module Demo.HigherOrder where

applyTwice :: (a -> a) -> a -> a
applyTwice f x = f (f x)

compose2 :: (b -> c) -> (a -> b) -> a -> c
compose2 f g x = f (g x)

mapBoth :: (a -> b) -> (a, a) -> (b, b)
mapBoth f (x, y) = (f x, f y)

fix2 :: (a -> a) -> a -> a
fix2 f = f . fix2 f
