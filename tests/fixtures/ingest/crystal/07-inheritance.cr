# Abstract classes and overriding.
abstract class Animal
  abstract def speak : String

  def describe : String
    "#{self.class}: #{speak}"
  end
end

class Dog < Animal
  def speak : String
    "woof"
  end
end
