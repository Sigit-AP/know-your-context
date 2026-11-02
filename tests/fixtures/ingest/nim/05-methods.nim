## method dispatch on ref objects.
type
  Animal = ref object of RootObj
  Dog = ref object of Animal

method speak(a: Animal): string {.base.} =
  "..."

method speak(d: Dog): string =
  procCall Animal(d).speak() & " woof"

proc makeDog(): Dog =
  Dog()
