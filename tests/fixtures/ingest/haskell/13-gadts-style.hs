-- Multi-parameter-ish types via existential-free design.
module Demo.Gadt where

data Expr
  = Lit Int
  | Add Expr Expr
  | EqE Expr Expr

eval :: Expr -> Int
eval (Lit n) = n
eval (Add a b) = eval a + eval b
eval (EqE a b) = if eval a == eval b then 1 else 0

render :: Expr -> String
render (Lit n) = show n
render (Add a b) = "(" ++ render a ++ "+" ++ render b ++ ")"
render (EqE a b) = "(" ++ render a ++ "==" ++ render b ++ ")"
