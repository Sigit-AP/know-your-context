-- List comprehensions and parallel-style zips.
module Demo.Comprehensions where

pairs :: [a] -> [b] -> [(a, b)]
pairs xs ys = [(x, y) | x <- xs, y <- ys]

evens :: [Int] -> [Int]
evens xs = [x | x <- xs, even x]

pythagorean :: Int -> [(Int, Int, Int)]
pythagorean n =
  [ (a, b, c)
  | a <- [1 .. n]
  , b <- [a .. n]
  , c <- [b .. n]
  , a * a + b * b == c * c
  ]
