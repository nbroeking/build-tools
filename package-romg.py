#!/usr/bin/python

import sys, re, argparse, os, subprocess, shutil, struct, binascii
import json
import logging
import tempfile
import tarfile

def __make_parser():
    p = argparse.ArgumentParser(description='This packages up modules and a base into a raw omg file')
    p.add_argument('-n', '--name', type=str, help='the ROMG name', default=None, required = True)
    p.add_argument('-V', '--version', type=str, help='the ROMG version', default = None, required = True)
    p.add_argument('--branch', type=str, help='the ROMG branch', default = None, required = False)
    p.add_argument('-b', '--base', type=str, help='the base packaged for the ROMG', default=None, required = True)
    p.add_argument('-m', '--modules', nargs='+', help='path(s) to module packages that should be included in the omg. multiple modules separated by spaces', required = True)
    p.add_argument('-o', '--overlays', nargs='*', help='path(s) to overlays that should be overlayed in the omg. multiple overlays separated by spaces', default=[], required = False)
    p.add_argument('-d', '--output-directory', type=str, help='optional output direcotry if not given CWD will be used', default = './')
    p.add_argument('-v', '--verbose', action='store_true')
    p.add_argument('-a', '--pre-package', action='append', dest='pre_package_scripts', default=[], help='Optional script(s) that will be run just before the romg is packaged that can be used to minifiy or tweak modules')
    return p

class romgBuilder(object):
    def __init__(self, pathToBase, logger, tmpDir, name, version, branch=None):
        self.tmpDir = tmpDir
        self.logger = logger
        self.logger.debug("Adding base %s", pathToBase)
        self.info = {'name': name, 'version': version, 'modules': [], 'overlays': {}, 'arch': 'x64'}
        if None != branch:
            self.info['branch'] = branch
        baseInfo = self.__readModuleJson(pathToBase)
        self.info['base'] = {'name': baseInfo['name'], 'version': baseInfo['version']}
        self.info['modules'].append(self.__readModuleJson(pathToBase))
        self.__extractTgz(pathToBase)
        self.moduleDir = os.path.join('data', 'base', 'modules', 'modules')
        os.makedirs(os.path.abspath(os.path.join(self.tmpDir, self.moduleDir)))
        self.yarnCacheDir = None
        self.yarnCacheDir = os.path.join(self.tmpDir, 'support', 'yarn-cache')

    def __extractTgz(self, tgzPath, relativeDir='.'):
        extractDir = os.path.abspath(os.path.join(self.tmpDir, relativeDir))
        self.logger.debug('Extracting %s to %s', tgzPath, extractDir)
        tf = tarfile.open(tgzPath, 'r')
        tf.extractall(extractDir)

    def __extractJsonFromTgz(self, tgzPath, filepath):
        tf = tarfile.open(tgzPath, 'r')
        contents = json.loads(tf.extractfile(filepath).read())
        return contents

    def __readModuleJson(self, moduleTgzPath):
        tf = tarfile.open(moduleTgzPath, 'r')
        moduleJson = self.__extractJsonFromTgz(moduleTgzPath, 'module.json')
        if not moduleJson.has_key('dependencies'):
            moduleJson['dependencies'] = {}
        return {'name': moduleJson['name'], 'version': moduleJson['version'], 'dependencies': moduleJson['dependencies']}

    def addModule(self, moduleTgzPath):
        self.logger.debug("Adding module %s", moduleTgzPath)
        moduleInfo = self.__readModuleJson(moduleTgzPath)
        self.info['modules'].append(moduleInfo)
        relModuleDir = os.path.join(self.moduleDir, str(moduleInfo['name']))
        self.__extractTgz(moduleTgzPath, relModuleDir)
        self.__updateYarnCache(os.path.join(self.tmpDir, relModuleDir))

    def __readOverlayJson(self, overlayTgzPath):
        tf = tarfile.open(overlayTgzPath, 'r')
        overlayJson = self.__extractJsonFromTgz(overlayTgzPath, 'overlay.json')
        return {'name': overlayJson['name'], 'version': overlayJson['version']}

    def addOverlay(self, overlayTgzPath):
        self.logger.debug("Adding overlay %s", overlayTgzPath)
        overlayInfo = self.__readOverlayJson(overlayTgzPath)
        self.info['overlays'][overlayInfo['name']] = {'version': overlayInfo['version']}
        self.__extractTgz(overlayTgzPath)

    def writeRomg(self, outputDir, branch=None):
        if self.info.has_key('branch'):
            sRomgFilename = '%s_%s_%s.romg' % (self.info['name'], self.info['branch'], self.info['version'])
            sRomgInfoFilename = '%s_%s_%s_header.json' % (self.info['name'], self.info['branch'], self.info['version'])
        else:
            sRomgFilename = '%s_%s.romg' % (self.info['name'], self.info['version'])
            sRomgInfoFilename = '%s_%s_header.json' % (self.info['name'], self.info['version'])
        sRomgFilepath = os.path.join(outputDir, sRomgFilename)
        sRomgInfoFilepath = os.path.join(outputDir, sRomgInfoFilename)
        self.logger.debug('Outputing to %s %s', sRomgFilepath, sRomgInfoFilepath)
        with tarfile.open(sRomgFilepath, "w:gz") as tar:
            tar.add(self.tmpDir, arcname='./')
        with open(sRomgInfoFilepath, 'w') as infoFile:
            infoFile.write(json.dumps(self.info, indent=2, separators=(',', ': ')))

    def __updateYarnCache(self, moduleDir):
        """
        This will update the global omg yarn cache dir (yarn-cache) with the cache dir from the module this is done
        by rsync command to de-duplicate dependencies across all modules, if the module does not have a yarn cache
        dir at support/yarn-cache this step will be skipped.  If it does exist it will be deleted after syncing to the
        global omg yarn-cache dir
        """
        moduleCacheDir = os.path.join(os.path.abspath(os.path.join(moduleDir, 'support', 'yarn-cache')))
        if os.path.isdir(moduleCacheDir):
            p = subprocess.Popen(['rsync', '-a', moduleCacheDir + '/', self.yarnCacheDir + '/'])
            p.wait()
            if p.returncode != 0:
                sys.stderr.write('Failed to sync yarn cache for %s\n' % (moduleDir))
                sys.exit(1)
            shutil.rmtree(moduleCacheDir)


def checkFileArg(fileName, errorStr):
    if not os.path.exists(fileName):
        sys.stderr.write(errorStr + ' file not found')
        sys.exit(1)
    try:
        return os.path.abspath(fileName)
    except Exception:
        sys.stderr.write(errorStr + ' invlid path')
        sys.exit(1)

def run_pre_package_scripts(scripts, buildDir):
    print 'Scripts: ' , scripts
    my_env = os.environ.copy()

    for script in scripts:
        print "Running " + script
        try:
            args = script.split(' ')
            ret = subprocess.call(args, cwd=buildDir, env=my_env)
            if 0 != ret:
                print 'Failed to run ' , ret
        except Exception as e:
            print 'Failed to run script ' , e


def __main(argv):
    parser = __make_parser()
    settings = parser.parse_args(argv[1:])
    logger = logging.Logger('package-romg')
    sh = logging.StreamHandler()
    if settings.verbose:
        sh.setLevel(logging.DEBUG)
    else:
        sh.setLevel(logging.ERROR)
    logger.addHandler(sh)
    #get absolute paths and check file inputs for existence
    settings.base = checkFileArg(settings.base, 'Invalid argument for base %s' % (settings.base))
    settings.modules = [checkFileArg(modulePath, 'Error invalid module specified %s' % (modulePath)) for modulePath in settings.modules]
    settings.overlays = [checkFileArg(overlayPath, 'Error invalid overlay specified %s' % (overlayPath)) for overlayPath in settings.overlays]
    settings.output_directory = checkFileArg(settings.output_directory, 'Error invalid output dir')
    logger.debug("Base: %s Modules: %s Overlays: %s", settings.base, settings.modules, settings.overlays)

    tmpDir = tempfile.mkdtemp(prefix='romg-')
    logger.debug('Using temp dir %s', tmpDir)

    romg = romgBuilder(settings.base, logger, tmpDir, settings.name, settings.version, settings.branch)
    for module in settings.modules:
        romg.addModule(module)
    for overlay in settings.overlays:
        romg.addOverlay(overlay)

    run_pre_package_scripts(settings.pre_package_scripts, tmpDir)

    romg.writeRomg(settings.output_directory)
    #clean up temp dir
    shutil.rmtree(tmpDir)
    sys.exit(0)

if __name__ == "__main__":
    __main(sys.argv)
