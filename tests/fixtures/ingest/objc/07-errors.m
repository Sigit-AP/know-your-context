// NSError** pattern and @try/@catch/@finally.
#import <Foundation/Foundation.h>

BOOL writeText(NSString *text, NSString *path, NSError **error) {
  @try {
    return [text writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:error];
  } @catch (NSException *exception) {
    if (error) {
      *error = [NSError errorWithDomain:@"demo" code:1 userInfo:nil];
    }
    return NO;
  } @finally {
    // nothing to release under ARC
  }
}
