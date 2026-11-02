-- Top-level functions with signatures.
module Demo.Functions where

add :: Int -> Int -> Int
add a b = a + b

greet :: String -> String -> String
greet greeting name = greeting ++ ", " ++ name

clamp :: Int -> Int -> Int -> Int
clamp low high value
  | value < low = low
  | value > high = high
  | otherwise = value
