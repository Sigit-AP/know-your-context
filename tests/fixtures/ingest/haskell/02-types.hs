-- data, newtype and type synonyms.
module Demo.Types where

data Point = Point
  { px :: Int
  , py :: Int
  }

data Colour = Red | Green | Blue
  deriving (Eq, Show)

newtype UserId = UserId Int

type Name = String

origin :: Point
origin = Point 0 0
