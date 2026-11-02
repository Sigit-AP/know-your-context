// Mixins and mixin classes.
mixin Walkable {
  String walk() => 'walking';
}

mixin Swimmable {
  String swim() => 'swimming';
}

mixin class Speaker {
  String speak() => '...';
}

class Duck extends Object with Walkable, Swimmable {}

class Dog with Walkable {}
