// For comprehensions over options and lists.
package demo

def pairwiseSum(a: Option[Int], b: Option[Int]): Option[Int] =
  for {
    x <- a
    y <- b
  } yield x + y

def combinations(xs: List[Int], ys: List[Int]): List[(Int, Int)] =
  for {
    x <- xs
    y <- ys
    if x < y
  } yield (x, y)
