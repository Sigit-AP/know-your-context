-- Maybe/Either error handling and exceptions.
module Demo.Errors where

data ConfigError = Missing String | Invalid String
  deriving (Eq, Show)

readConfig :: [(String, String)] -> String -> Either String String
readConfig entries key =
  case lookup key entries of
    Just value -> Right value
    Nothing -> Left ("missing config key: " ++ key)

readConfigDefault :: [(String, String)] -> String -> String -> String
readConfigDefault entries key fallback =
  case readConfig entries key of
    Right value -> value
    Left _ -> fallback
