-- Type classes and instances.
module Demo.Classes where

class Shape a where
  area :: a -> Double
  nameOf :: a -> String

data Circle = Circle Double

instance Shape Circle where
  area (Circle r) = 3.14159 * r * r
  nameOf _ = "circle"

totalArea :: (Shape a) => [a] -> Double
totalArea = sum . map area
