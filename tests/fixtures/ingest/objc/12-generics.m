// Lightweight generics on collections and classes.
#import <Foundation/Foundation.h>

@interface Box<__covariant T> : NSObject
@property(nonatomic) T item;
- (T)unwrap;
@end

@implementation Box
- (id)unwrap {
  return self.item;
}
@end

NSArray<NSString *> *namesFrom(NSArray<NSString *> *input) { return input; }
