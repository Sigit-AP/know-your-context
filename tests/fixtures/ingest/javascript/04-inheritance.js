// extends, super and static members.
class Animal {
  speak() {
    return "...";
  }
}

class Dog extends Animal {
  speak() {
    return super.speak() + " woof";
  }

  static create() {
    return new Dog();
  }
}
